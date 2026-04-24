"""Output Sanitizer Module for LLM Benchmark Evaluation.

This module provides sanitization of LLM output before it reaches the judge pipeline.
It intercepts forbidden substrings in output and applies configurable strategies.

Sanitization Strategies
------------------------
- STRICT: Completely removes forbidden tokens from output
- REPLACE: Replaces forbidden tokens with [FILTERED] marker
- SOFT: Only filters tokens in critical output positions (start/end of sentences)

Example
-------
```python
from polaris.cells.llm.evaluation.internal.output_sanitizer import (
    OutputSanitizer,
    SanitizationStrategy,
)

sanitizer = OutputSanitizer(
    forbidden_tokens=["stable_join", "forbidden_word"],
    strategy=SanitizationStrategy.REPLACE,
)

output = "Use stable_join function to join data"
sanitized, was_modified = sanitizer.sanitize(output)
# sanitized: "Use [FILTERED] function to join data"
# was_modified: True
```
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from polaris.kernelone.benchmark.unified_models import UnifiedBenchmarkCase

    # Backward compatibility with legacy agentic benchmark models
    from .benchmark_models import AgenticBenchmarkCase as _LegacyAgenticBenchmarkCase

    _BenchmarkCaseType: TypeAlias = UnifiedBenchmarkCase | _LegacyAgenticBenchmarkCase
else:
    _BenchmarkCaseType: TypeAlias = Any


class SanitizationStrategy(Enum):
    """Sanitization strategy for forbidden tokens.

    Attributes:
        STRICT: Completely removes forbidden tokens from output.
        REPLACE: Replaces forbidden tokens with [FILTERED] marker.
        SOFT: Only filters tokens in critical positions (start/end of sentences).
    """

    STRICT = "strict"
    REPLACE = "replace"
    SOFT = "soft"


# Default marker used for REPLACE strategy
DEFAULT_FILTER_MARKER = "[FILTERED]"


@dataclass(frozen=True)
class SanitizationResult:
    """Result of a sanitization operation.

    Attributes:
        sanitized_output: The output after sanitization.
        was_modified: Whether the output was modified.
        matched_tokens: List of tokens that were sanitized.
        strategy_used: The sanitization strategy that was applied.
    """

    sanitized_output: str
    was_modified: bool
    matched_tokens: tuple[str, ...] = field(default_factory=tuple)
    strategy_used: SanitizationStrategy = SanitizationStrategy.STRICT


@dataclass
class OutputSanitizer:
    """Sanitizer for LLM output with configurable forbidden tokens and strategies.

    This sanitizer intercepts forbidden substrings in output before they reach
    the judge pipeline. It supports multiple sanitization strategies and can
    be configured with custom synonym mappings.

    Attributes:
        forbidden_tokens: Tuple of forbidden substring tokens.
        strategy: Sanitization strategy to apply.
        filter_marker: Marker used for REPLACE strategy.
        synonym_map: Optional mapping of forbidden tokens to allowed replacements.
        case_sensitive: Whether token matching should be case-sensitive.

    Example:
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden", "bad_word"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This contains forbidden content")
        # result.sanitized_output: "This contains [FILTERED] content"
    """

    forbidden_tokens: tuple[str, ...] = field(default_factory=tuple)
    strategy: SanitizationStrategy = SanitizationStrategy.STRICT
    filter_marker: str = DEFAULT_FILTER_MARKER
    synonym_map: dict[str, str] = field(default_factory=dict)
    case_sensitive: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize sanitizer configuration."""
        # Ensure filter_marker is not empty
        if not self.filter_marker:
            object.__setattr__(self, "filter_marker", DEFAULT_FILTER_MARKER)

        # Validate forbidden_tokens
        normalized_tokens: list[str] = []
        for token in self.forbidden_tokens:
            token_str = str(token or "").strip()
            if token_str:
                normalized_tokens.append(token_str)
        object.__setattr__(self, "forbidden_tokens", tuple(normalized_tokens))

        # Normalize synonym_map keys
        if self.synonym_map:
            normalized_synonyms: dict[str, str] = {}
            for key, value in self.synonym_map.items():
                key_str = str(key or "").strip()
                value_str = str(value or "").strip()
                if key_str and value_str:
                    normalized_synonyms[key_str] = value_str
            object.__setattr__(self, "synonym_map", normalized_synonyms)

    def _escape_for_regex(self, token: str) -> str:
        """Escape a token for use in regex pattern.

        Args:
            token: The token to escape.

        Returns:
            Escaped token safe for regex operations.
        """
        return re.escape(token)

    def _create_pattern(self, token: str) -> re.Pattern[str]:
        """Create a regex pattern for matching a token.

        Args:
            token: The token to match.

        Returns:
            Compiled regex pattern.
        """
        escaped = self._escape_for_regex(token)
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(escaped, flags)

    def _apply_strict(self, output: str, matched: list[str]) -> str:
        """Apply STRICT sanitization - completely remove tokens.

        Args:
            output: The original output.
            matched: List to append matched tokens to.

        Returns:
            Sanitized output with tokens removed.
        """
        result = output
        for token in self.forbidden_tokens:
            pattern = self._create_pattern(token)
            new_result, count = pattern.subn("", result)
            if count > 0:
                if token not in matched:
                    matched.append(token)
                result = new_result
        # Clean up multiple spaces created by removal
        result = re.sub(r"\s{2,}", " ", result)
        return result.strip()

    def _apply_replace(self, output: str, matched: list[str]) -> str:
        """Apply REPLACE sanitization - replace tokens with marker.

        Args:
            output: The original output.
            matched: List to append matched tokens to.

        Returns:
            Sanitized output with tokens replaced.
        """
        result = output
        for token in self.forbidden_tokens:
            # Check if we have a synonym for this token
            replacement = self.synonym_map.get(token, self.filter_marker)
            pattern = self._create_pattern(token)
            new_result, count = pattern.subn(replacement, result)
            if count > 0:
                if token not in matched:
                    matched.append(token)
                result = new_result
        return result

    def _apply_soft(self, output: str, matched: list[str]) -> str:
        """Apply SOFT sanitization - only filter critical positions.

        Filters tokens that appear at:
        - Start/end of sentences
        - Start/end of the entire output
        - Inside parentheses

        Args:
            output: The original output.
            matched: List to append matched tokens to.

        Returns:
            Sanitized output with critical-position tokens filtered.
        """
        result = output
        flags = 0 if self.case_sensitive else re.IGNORECASE

        for token in self.forbidden_tokens:
            escaped = self._escape_for_regex(token)

            # Pattern for tokens inside parentheses
            paren_pattern = re.compile(rf"\({escaped}|{escaped}\)", flags)
            new_result, count1 = paren_pattern.subn(self.filter_marker, result)
            if count1 > 0:
                result = new_result
                if token not in matched:
                    matched.append(token)

        return result

    def sanitize(self, output: str) -> SanitizationResult:
        """Sanitize output by applying configured strategy to forbidden tokens.

        Args:
            output: The raw LLM output to sanitize.

        Returns:
            SanitizationResult containing sanitized output and metadata.
        """
        if not output or not self.forbidden_tokens:
            return SanitizationResult(
                sanitized_output=str(output or ""),
                was_modified=False,
                matched_tokens=(),
                strategy_used=self.strategy,
            )

        matched: list[str] = []
        original = str(output)

        if self.strategy == SanitizationStrategy.STRICT:
            sanitized = self._apply_strict(original, matched)
        elif self.strategy == SanitizationStrategy.REPLACE:
            sanitized = self._apply_replace(original, matched)
        elif self.strategy == SanitizationStrategy.SOFT:
            sanitized = self._apply_soft(original, matched)
        else:
            sanitized = original

        was_modified = len(matched) > 0 and sanitized != original

        return SanitizationResult(
            sanitized_output=sanitized,
            was_modified=was_modified,
            matched_tokens=tuple(matched),
            strategy_used=self.strategy,
        )

    def sanitize_case_output(
        self,
        output: str,
        forbidden_output_substrings: tuple[str, ...],
    ) -> SanitizationResult:
        """Sanitize output using case-specific forbidden substrings.

        This method creates a temporary sanitizer with the provided
        forbidden tokens, applies sanitization, and returns the result.
        The original sanitizer configuration remains unchanged.

        Args:
            output: The raw LLM output to sanitize.
            forbidden_output_substrings: Tuple of forbidden substrings
                from the benchmark case.

        Returns:
            SanitizationResult containing sanitized output and metadata.
        """
        if not output or not forbidden_output_substrings:
            return SanitizationResult(
                sanitized_output=str(output or ""),
                was_modified=False,
                matched_tokens=(),
                strategy_used=self.strategy,
            )

        # Create temporary sanitizer with case-specific tokens
        temp_sanitizer = OutputSanitizer(
            forbidden_tokens=forbidden_output_substrings,
            strategy=self.strategy,
            filter_marker=self.filter_marker,
            synonym_map=self.synonym_map,
            case_sensitive=self.case_sensitive,
        )
        return temp_sanitizer.sanitize(output)


def sanitize_observation_output(
    raw_output: str,
    raw_thinking: str,
    case: _BenchmarkCaseType,
    strategy: SanitizationStrategy = SanitizationStrategy.REPLACE,
    filter_marker: str = DEFAULT_FILTER_MARKER,
) -> tuple[str, str, SanitizationResult, SanitizationResult]:
    """Sanitize both output and thinking fields of a benchmark observation.

    This function applies sanitization to both output and thinking fields
    using the forbidden_output_substrings from the benchmark case.

    Args:
        raw_output: The raw output from LLM.
        raw_thinking: The raw thinking content from LLM.
        case: The benchmark case containing forbidden substrings.
        strategy: Sanitization strategy to apply.
        filter_marker: Marker for REPLACE strategy.

    Returns:
        Tuple of (sanitized_output, sanitized_thinking, output_result, thinking_result).
    """
    sanitizer = OutputSanitizer(
        forbidden_tokens=case.judge.forbidden_output_substrings,
        strategy=strategy,
        filter_marker=filter_marker,
    )

    output_result = sanitizer.sanitize_case_output(raw_output, case.judge.forbidden_output_substrings)
    thinking_result = sanitizer.sanitize_case_output(raw_thinking, case.judge.forbidden_output_substrings)

    return (
        output_result.sanitized_output,
        thinking_result.sanitized_output,
        output_result,
        thinking_result,
    )


def create_sanitizer_from_case(
    case: _BenchmarkCaseType,
    strategy: SanitizationStrategy = SanitizationStrategy.REPLACE,
    filter_marker: str = DEFAULT_FILTER_MARKER,
    synonym_map: dict[str, str] | None = None,
) -> OutputSanitizer:
    """Create an OutputSanitizer from a benchmark case configuration.

    Args:
        case: The benchmark case to create sanitizer from.
        strategy: Sanitization strategy to apply.
        filter_marker: Marker for REPLACE strategy.
        synonym_map: Optional custom synonym mappings.

    Returns:
        Configured OutputSanitizer instance.
    """
    return OutputSanitizer(
        forbidden_tokens=case.judge.forbidden_output_substrings,
        strategy=strategy,
        filter_marker=filter_marker,
        synonym_map=synonym_map or {},
    )
