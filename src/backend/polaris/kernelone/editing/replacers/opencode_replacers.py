"""Edit Replacers for fuzzy matching with fallback chain.

This module provides multiple strategies for finding text in content,
inspired by OpenCode's approach with Levenshtein-based similarity matching.

Strategies (in priority order):
1. SimpleReplacer - Exact string match
2. LineTrimmedReplacer - Line-by-line trim matching
3. BlockAnchorReplacer - First/last line anchors with similarity
4. WhitespaceNormalizedReplacer - Normalizes whitespace
5. IndentationFlexibleReplacer - Removes common indentation
6. EscapeNormalizedReplacer - Handles escaped characters
7. TrimmedBoundaryReplacer - Matches trimmed boundaries
8. ContextAwareReplacer - Context anchors with fuzzy middle
9. MultiOccurrenceReplacer - All exact matches

Reference: OpenCode packages/opencode/src/tool/edit.ts
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

# =============================================================================
# Levenshtein Distance
# =============================================================================


def levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein distance between two strings.

    Uses O(min(n,m)) space optimization by keeping only two rows
    of the DP matrix instead of the full n*m matrix.

    Args:
        a: First string
        b: Second string

    Returns:
        Edit distance between the strings
    """
    # Ensure 'a' is the shorter string for space efficiency
    if len(a) > len(b):
        a, b = b, a

    # Edge cases
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Use two rows instead of full matrix for O(min(n,m)) space
    # previous_row represents the previous DP row
    # current_row represents the current DP row being computed
    previous_row = list(range(len(b) + 1))

    for i, char_a in enumerate(a):
        # Start new row: current_row[j] represents DP[i+1][j]
        current_row = [i + 1]  # DP[i+1][0] = i + 1

        for j, char_b in enumerate(b):
            # Calculate insertion, deletion, and substitution costs
            insertions = previous_row[j + 1] + 1  # DP[i][j+1] + 1
            deletions = current_row[j] + 1  # DP[i+1][j] + 1
            substitutions = previous_row[j] + (char_a != char_b)  # DP[i][j] + (char_a != char_b)

            # Store minimum cost in current row
            current_row.append(min(insertions, deletions, substitutions))

        # Move current row to previous for next iteration
        previous_row = current_row

    # Last element of final row is the edit distance
    return previous_row[-1]


def string_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings (0.0 to 1.0).

    Args:
        a: First string
        b: Second string

    Returns:
        Similarity ratio (1.0 = identical)
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    max_len = max(len(a), len(b))
    distance = levenshtein_distance(a, b)
    return 1.0 - (distance / max_len)


# =============================================================================
# Line Utilities
# =============================================================================


def normalize_line_endings(text: str) -> str:
    """Normalize line endings to \\n.

    Args:
        text: Text with mixed line endings

    Returns:
        Text with only \\n line endings
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[str]:
    """Split text into lines preserving line endings.

    Args:
        text: Text to split

    Returns:
        List of lines (including trailing newlines except last)
    """
    if not text:
        return []
    lines = text.splitlines(keepends=True)
    # Handle case where last line doesn't have newline
    if lines and not lines[-1].endswith("\n"):
        # Last line has no newline
        pass
    return lines


# =============================================================================
# Simple Replacer
# =============================================================================


class SimpleReplacer:
    """Exact string match replacer.

    This is the most precise replacer - it yields the exact search
    string if found in the content.
    """

    name = "simple"
    priority = 10

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find exact matches of search in content.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            The search string if found
        """
        if search in content:
            yield search


# =============================================================================
# Line Trimmed Replacer
# =============================================================================


class LineTrimmedReplacer:
    """Line-by-line trimmed matching replacer.

    Matches content where each line, when trimmed, matches the
    corresponding trimmed search line.
    """

    name = "line_trimmed"
    priority = 20

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find line-trimmed matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        content_lines = split_lines(normalize_line_endings(content))
        search_lines = split_lines(normalize_line_endings(search))

        # Remove trailing empty line if present
        if search_lines and search_lines[-1].strip() == "" and search_lines[-1].endswith("\n"):
            search_lines = search_lines[:-1]

        if not search_lines:
            return

        search_len = len(search_lines)

        for i in range(len(content_lines) - search_len + 1):
            # Check if all lines match when trimmed
            matches = True
            for j in range(search_len):
                content_trimmed = content_lines[i + j].strip()
                search_trimmed = search_lines[j].strip()
                if content_trimmed != search_trimmed:
                    matches = False
                    break

            if matches:
                # Reconstruct the original text
                match_start = 0
                for k in range(i):
                    match_start += len(content_lines[k])

                match_end = match_start
                for k in range(search_len):
                    match_end += len(content_lines[i + k])

                yield content[match_start:match_end]


# =============================================================================
# Block Anchor Replacer
# =============================================================================


# Similarity thresholds
SINGLE_CANDIDATE_THRESHOLD = 0.0
MULTIPLE_CANDIDATES_THRESHOLD = 0.3


class BlockAnchorReplacer:
    """Block anchor-based matching with Levenshtein similarity.

    Uses first and last lines as anchors, then checks middle content
    similarity using Levenshtein distance.
    """

    name = "block_anchor"
    priority = 30

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find block anchor matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        content_lines = split_lines(normalize_line_endings(content))
        search_lines = split_lines(normalize_line_endings(search))

        # Need at least 3 lines for block anchoring
        if len(search_lines) < 3:
            return

        # Remove trailing empty line
        if search_lines and search_lines[-1].strip() == "":
            search_lines = search_lines[:-1]

        if len(search_lines) < 3:
            return

        first_anchor = search_lines[0].strip()
        last_anchor = search_lines[-1].strip()
        len(search_lines)

        # Find all candidate positions where both anchors match
        candidates: list[tuple[int, int]] = []
        for i, line in enumerate(content_lines):
            if line.strip() != first_anchor:
                continue

            # Look for matching last line
            for j in range(i + 2, len(content_lines)):
                if content_lines[j].strip() == last_anchor:
                    candidates.append((i, j))
                    break  # Only first occurrence of last line

        if not candidates:
            return

        if len(candidates) == 1:
            # Single candidate - use relaxed threshold
            start_line, end_line = candidates[0]
            match = BlockAnchorReplacer._check_similarity(
                content, content_lines, search_lines, start_line, end_line, SINGLE_CANDIDATE_THRESHOLD
            )
            if match:
                yield match
        else:
            # Multiple candidates - find best by similarity, only yield if above threshold
            best_match: str | None = None
            best_similarity = -1.0

            for start_line, end_line in candidates:
                similarity = BlockAnchorReplacer._calculate_similarity(
                    content_lines, search_lines, start_line, end_line
                )
                # Only update best if similarity is strictly higher
                # AND meets the threshold requirement
                if similarity > best_similarity and similarity >= MULTIPLE_CANDIDATES_THRESHOLD:
                    best_similarity = similarity
                    match = BlockAnchorReplacer._check_similarity(
                        content, content_lines, search_lines, start_line, end_line, MULTIPLE_CANDIDATES_THRESHOLD
                    )
                    if match:
                        best_match = match

            if best_match:
                yield best_match

    @staticmethod
    def _calculate_similarity(
        content_lines: list[str],
        search_lines: list[str],
        start_line: int,
        end_line: int,
    ) -> float:
        """Calculate similarity score for a block."""
        actual_block_size = end_line - start_line + 1
        search_block_size = len(search_lines)

        # Only compare middle lines
        lines_to_compare = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_compare <= 0:
            return 1.0

        total_similarity = 0.0
        for j in range(1, search_block_size - 1):
            if j >= actual_block_size - 1:
                break
            content_line = content_lines[start_line + j].strip()
            search_line = search_lines[j].strip()
            total_similarity += string_similarity(content_line, search_line)

        return total_similarity / lines_to_compare

    @staticmethod
    def _check_similarity(
        content: str,
        content_lines: list[str],
        search_lines: list[str],
        start_line: int,
        end_line: int,
        threshold: float,
    ) -> str | None:
        """Check if block meets similarity threshold."""
        similarity = BlockAnchorReplacer._calculate_similarity(content_lines, search_lines, start_line, end_line)

        if similarity >= threshold:
            # Calculate character positions
            match_start = 0
            for k in range(start_line):
                match_start += len(content_lines[k])

            match_end = match_start
            for k in range(start_line, end_line + 1):
                match_end += len(content_lines[k])

            return content[match_start:match_end]
        return None


# =============================================================================
# Whitespace Normalized Replacer
# =============================================================================


class WhitespaceNormalizedReplacer:
    """Whitespace-normalized matching replacer.

    Normalizes all whitespace to single spaces before comparing.
    """

    name = "whitespace_normalized"
    priority = 40

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Normalize whitespace in text."""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find whitespace-normalized matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        normalized_search = WhitespaceNormalizedReplacer._normalize_whitespace(search)

        # Single line matching
        lines = split_lines(normalize_line_endings(content))
        for _i, line in enumerate(lines):
            normalized_line = WhitespaceNormalizedReplacer._normalize_whitespace(line)
            if normalized_line == normalized_search:
                yield line

        # Multi-line matching
        search_lines = split_lines(normalize_line_endings(search))
        if len(search_lines) > 1:
            for i in range(len(lines) - len(search_lines) + 1):
                block = "".join(lines[i : i + len(search_lines)])
                normalized_block = WhitespaceNormalizedReplacer._normalize_whitespace(block)
                if normalized_block == normalized_search:
                    yield block


# =============================================================================
# Indentation Flexible Replacer
# =============================================================================


class IndentationFlexibleReplacer:
    """Indentation-flexible matching replacer.

    Removes common leading indentation before comparing.
    """

    name = "indentation_flexible"
    priority = 50

    @staticmethod
    def _remove_indentation(text: str) -> str:
        """Remove common indentation from text."""
        lines = text.split("\n")
        non_empty_lines = [line for line in lines if line.strip()]

        if not non_empty_lines:
            return text

        # Find minimum indentation
        min_indent = float("inf")
        for line in non_empty_lines:
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                min_indent = min(min_indent, indent)

        if min_indent == float("inf") or min_indent == 0:
            return text

        # Remove common indentation
        result_lines = []
        for line in lines:
            indent = int(min_indent)
            if line.strip():
                result_lines.append(line[indent:])
            else:
                result_lines.append("")
        return "\n".join(result_lines)

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find indentation-flexible matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        normalized_search = IndentationFlexibleReplacer._remove_indentation(search)
        content_lines = split_lines(normalize_line_endings(content))
        search_lines = split_lines(normalize_line_endings(search))
        search_len = len(search_lines)

        for i in range(len(content_lines) - search_len + 1):
            block = "".join(content_lines[i : i + search_len])
            normalized_block = IndentationFlexibleReplacer._remove_indentation(block)
            if normalized_block == normalized_search:
                yield block


# =============================================================================
# Escape Normalized Replacer
# =============================================================================


class EscapeNormalizedReplacer:
    """Escape sequence-normalized matching replacer.

    Handles escaped characters like \\n, \\t, etc.
    """

    name = "escape_normalized"
    priority = 60

    # Pre-compiled regex for escape sequences
    _ESCAPE_PATTERN = re.compile(r'\\(x[0-9a-fA-F]{2}|[nrt\\\'"`])')

    @staticmethod
    def _unescape_string(text: str) -> str:
        """Unescape common escape sequences.

        Uses regex substitution to avoid ordering issues with sequential replacements.
        """

        def replace_escape(match: re.Match) -> str:
            """Replace a single escape sequence with its character value."""
            escape_map: dict[str, str] = {
                "\\n": "\n",
                "\\t": "\t",
                "\\r": "\r",
                "\\\\": "\\",
                "\\'": "'",
                '\\"': '"',
                "\\`": "`",
            }
            seq = match.group(0)  # Always use the full match
            if seq.startswith("\\x") or seq.startswith("\\X"):
                # Hex escape: \xAB
                return chr(int(seq[2:], 16))
            return escape_map.get(seq, seq)

        return EscapeNormalizedReplacer._ESCAPE_PATTERN.sub(replace_escape, text)

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find escape-normalized matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        unescaped_search = EscapeNormalizedReplacer._unescape_string(search)

        # Try direct match with unescaped search
        if unescaped_search in content:
            yield unescaped_search

        # Try finding escaped versions in content
        content_lines = split_lines(normalize_line_endings(content))
        search_lines = split_lines(normalize_line_endings(search))

        for i in range(len(content_lines) - len(search_lines) + 1):
            block = "".join(content_lines[i : i + len(search_lines)])
            unescaped_block = EscapeNormalizedReplacer._unescape_string(block)
            if unescaped_block == unescaped_search:
                yield block


# =============================================================================
# Trimmed Boundary Replacer
# =============================================================================


class TrimmedBoundaryReplacer:
    """Trimmed boundary matching replacer.

    Matches text that, when trimmed, equals the trimmed search.
    """

    name = "trimmed_boundary"
    priority = 70

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find trimmed boundary matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        trimmed_search = search.strip()

        if trimmed_search == search:
            # Already trimmed, no point trying
            return

        # Try to find trimmed version
        if trimmed_search in content:
            yield trimmed_search

        # Try finding blocks where trimmed content matches
        content_lines = split_lines(normalize_line_endings(content))
        search_lines = split_lines(normalize_line_endings(search))

        for i in range(len(content_lines) - len(search_lines) + 1):
            block = "".join(content_lines[i : i + len(search_lines)])
            if block.strip() == trimmed_search:
                yield block


# =============================================================================
# Context Aware Replacer
# =============================================================================


class ContextAwareReplacer:
    """Context-aware matching replacer.

    Uses first and last lines as anchors, with fuzzy matching
    for middle content (50% line match threshold).
    """

    name = "context_aware"
    priority = 80

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find context-aware matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text from content
        """
        search_lines = split_lines(normalize_line_endings(search))

        # Need at least 3 lines for meaningful context
        if len(search_lines) < 3:
            return

        # Remove trailing empty line
        if search_lines[-1].strip() == "":
            search_lines = search_lines[:-1]

        if len(search_lines) < 3:
            return

        content_lines = split_lines(normalize_line_endings(content))

        # Extract first and last lines as context anchors
        first_anchor = search_lines[0].strip()
        last_anchor = search_lines[-1].strip()

        # Find blocks that start and end with the context anchors
        for i, line in enumerate(content_lines):
            if line.strip() != first_anchor:
                continue

            # Look for matching last line
            for j in range(i + 2, len(content_lines)):
                if content_lines[j].strip() != last_anchor:
                    continue

                # Found a potential context block
                block_lines = content_lines[i : j + 1]
                block = "".join(block_lines)

                # Check if middle content has reasonable similarity
                if len(block_lines) == len(search_lines):
                    matching_lines = 0
                    total_non_empty = 0

                    for k in range(1, len(block_lines) - 1):
                        block_line = block_lines[k].strip()
                        search_line = search_lines[k].strip()

                        if block_line or search_line:
                            total_non_empty += 1
                            if block_line == search_line:
                                matching_lines += 1

                    # Require at least 50% match
                    if total_non_empty == 0 or matching_lines / total_non_empty >= 0.5:
                        yield block
                        return  # Only match first occurrence


# =============================================================================
# Multi Occurrence Replacer
# =============================================================================


class MultiOccurrenceReplacer:
    """Multi-occurrence replacer.

    Yields all exact matches, allowing the caller to handle
    multiple occurrences based on replaceAll parameter.
    """

    name = "multi_occurrence"
    priority = 90

    @staticmethod
    def find(content: str, search: str) -> Generator[str, None, None]:
        """Find all exact matches.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            The search string for each occurrence
        """
        start_index = 0
        while True:
            index = content.find(search, start_index)
            if index == -1:
                break
            yield search
            start_index = index + len(search)


# =============================================================================
# Replacer Registry
# =============================================================================


# Default replacer chain (ordered by priority)
DEFAULT_REPLACERS: list[type] = [
    SimpleReplacer,
    LineTrimmedReplacer,
    BlockAnchorReplacer,
    WhitespaceNormalizedReplacer,
    IndentationFlexibleReplacer,
    EscapeNormalizedReplacer,
    TrimmedBoundaryReplacer,
    ContextAwareReplacer,
    MultiOccurrenceReplacer,
]


def get_replacer_chain() -> list[type]:
    """Get the default replacer chain.

    Returns:
        List of replacer classes in priority order
    """
    return list(DEFAULT_REPLACERS)
