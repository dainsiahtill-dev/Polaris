"""Heuristic pre-processing cleaners for LLM output.

This module provides rules to clean common LLM output artifacts that interfere
with JSON parsing, such as natural language prefixes, trailing explanations,
and invisible Unicode characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Compiled regex patterns for efficiency
_NL_PREFIX_PATTERN = re.compile(
    r"^(?:here(?:'s| is) (?:the )?(?:json|result|output|data|answer|response|object|structure):?\s*"
    r"|(?:sure|of course|certainly)[,:]?\s*"
    r"|(?:as requested|here(?:goes|with)):?\s*)",
    re.IGNORECASE,
)

_TRAILING_EXPLANATION_PATTERN = re.compile(
    r"(?:\n\s*)?"
    r"(?:that's?.*|that is.*|this is.*|as requested.*|"
    r"i+s? (?:that|this).*|(?:please )?let me know.*|"
    r"feel free to.*|"
    r"(?:any )?(?:questions?|concerns?|follow[- ]?up).*|"
    r"hope (?:this|that|it) (?:helps?|works?).*|"
    r"let me know if.*)"
    r"$",
    re.IGNORECASE,
)

_WHITESPACE_NORMALIZE = re.compile(r"\s+")

_INVISIBLE_UNICODE_PATTERN = re.compile(r"[\u200b-\u200f\ufeff\u00ad\u061c\u200a-\u200f]")


@dataclass(frozen=True)
class CleaningResult:
    """Result of heuristic cleaning."""

    cleaned: str
    applied_rules: tuple[str, ...] = field(default_factory=tuple)
    changed: bool = False


class HeuristicCleaner:
    """Heuristic pre-processor for LLM output.

    Applies a series of regex-based cleaning rules to remove common
    LLM output artifacts that interfere with JSON parsing.

    Rules applied in order:
    1. Strip natural language prefixes ("Here's the JSON:", "Sure, here...")
    2. Strip trailing explanations ("This should work...", "Let me know if...")
    3. Normalize whitespace in code blocks
    4. Remove invisible Unicode characters
    5. Normalize line endings
    """

    def __init__(
        self,
        *,
        strip_prefixes: bool = True,
        strip_trailing: bool = True,
        normalize_whitespace: bool = True,
        remove_invisible: bool = True,
        strip_code_fences: bool = False,
    ) -> None:
        """Initialize cleaner with configurable rules.

        Args:
            strip_prefixes: Remove NL prefixes like "Here's the JSON:"
            strip_trailing: Remove trailing explanations
            normalize_whitespace: Collapse multiple spaces to single
            remove_invisible: Remove zero-width and similar Unicode
            strip_code_fences: Remove ```json ... ``` fences entirely
        """
        self._strip_prefixes = strip_prefixes
        self._strip_trailing = strip_trailing
        self._normalize_whitespace = normalize_whitespace
        self._remove_invisible = remove_invisible
        self._strip_code_fences = strip_code_fences

    def clean(self, text: str) -> CleaningResult:
        """Apply all cleaning rules to text.

        Args:
            text: Raw LLM output text

        Returns:
            CleaningResult with cleaned text and metadata
        """
        if not text:
            return CleaningResult(cleaned="", applied_rules=(), changed=False)

        applied: list[str] = []
        current = text

        # Rule 1: Strip natural language prefixes (loop to handle chained prefixes)
        if self._strip_prefixes:
            max_iterations = 5  # Safety limit
            for _ in range(max_iterations):
                original = current
                current = _NL_PREFIX_PATTERN.sub("", current)
                if current != original:
                    applied.append("strip_nl_prefix")
                    # Continue stripping if remaining looks like more prefix
                    # Stop if remaining starts with JSON content
                    remainder = current.lstrip()
                    if remainder.startswith(("{", "[")):
                        # Remaining starts with JSON - stop stripping
                        break
                else:
                    break  # No more prefixes to strip

        # Rule 2: Strip trailing explanations
        if self._strip_trailing:
            original = current
            current = _TRAILING_EXPLANATION_PATTERN.sub("", current)
            if current != original:
                applied.append("strip_trailing_explanation")

        # Rule 3: Normalize line endings (ALWAYS runs, before whitespace normalization)
        original = current
        current = current.replace("\r\n", "\n").replace("\r", "\n")
        if current != original:
            applied.append("normalize_line_endings")

        # Rule 4: Normalize whitespace
        if self._normalize_whitespace:
            original = current
            current = _WHITESPACE_NORMALIZE.sub(" ", current)
            if current != original:
                applied.append("normalize_whitespace")

        # Rule 5: Remove invisible Unicode
        if self._remove_invisible:
            original = current
            current = _INVISIBLE_UNICODE_PATTERN.sub("", current)
            if current != original:
                applied.append("remove_invisible_unicode")

        return CleaningResult(
            cleaned=current.strip(),
            applied_rules=tuple(applied),
            changed=len(applied) > 0,
        )

    def strip_code_fence(self, text: str) -> str:
        """Strip markdown code fence from JSON text.

        Args:
            text: Text that may contain ```json ... ``` fence

        Returns:
            Text with fence removed
        """
        # Match ```json ... ``` or ``` ... ```
        pattern = re.compile(
            r"^```(?:json)?\s*\n?(.*?)\n?```$",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.match(text.strip())
        if match:
            return match.group(1).strip()
        return text
