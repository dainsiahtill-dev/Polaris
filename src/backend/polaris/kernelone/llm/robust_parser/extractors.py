"""Multi-pattern JSON extraction for LLM output.

This module handles extraction of JSON data from various LLM output formats,
including code blocks, tags, and raw JSON strings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Multi-line JSON object pattern (handles nested structures)
_JSON_CODE_BLOCK_PATTERN = re.compile(
    r"(?P<fence>```|''')(?:\s*(?:json|javascript))?\s*"
    r"(?P<body>[\s\S]*?)(?P=fence)",
    re.IGNORECASE,
)

# Single-line JSON object pattern
_JSON_INLINE_PATTERN = re.compile(
    r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"  # Simplified, handles 1 level of nesting
)

# <output> tag pattern
_OUTPUT_TAG_PATTERN = re.compile(
    r"<output>([\s\S]*?)</output>",
    re.IGNORECASE,
)

# <result> tag pattern
_RESULT_TAG_PATTERN = re.compile(
    r"<result>([\s\S]*?)</result>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractionResult:
    """Result of JSON extraction."""

    data: dict[str, Any] | list[Any] | None
    format_found: str | None  # "code_block", "output_tag", "inline", "raw"
    raw_match: str | None
    error: str | None


class JSONExtractor:
    """Multi-pattern JSON extractor with progressive fallback.

    Tries extraction methods in order:
    1. JSON code block (```json ... ```)
    2. <output> tags
    3. <result> tags
    4. Inline JSON object
    5. Raw JSON (array or object)

    Each method can be disabled via constructor flags.
    """

    def __init__(
        self,
        *,
        use_code_blocks: bool = True,
        use_output_tags: bool = True,
        use_result_tags: bool = True,
        use_inline: bool = True,
        use_raw: bool = True,
        max_depth: int = 3,  # Max recursion depth for nested extraction
    ) -> None:
        """Initialize extractor with configurable methods.

        Args:
            use_code_blocks: Try ```json ... ``` extraction
            use_output_tags: Try <output>...</output> extraction
            use_result_tags: Try <result>...</result> extraction
            use_inline: Try inline JSON object extraction
            use_raw: Try raw JSON parsing
            max_depth: Max recursion depth for nested structures
        """
        self._use_code_blocks = use_code_blocks
        self._use_output_tags = use_output_tags
        self._use_result_tags = use_result_tags
        self._use_inline = use_inline
        self._use_raw = use_raw
        self._max_depth = max_depth

    def extract(self, text: str) -> ExtractionResult:
        """Extract JSON data from text.

        Args:
            text: Potentially JSON-containing text from LLM

        Returns:
            ExtractionResult with extracted data or error
        """
        if not text or not text.strip():
            return ExtractionResult(
                data=None,
                format_found=None,
                raw_match=None,
                error="Empty input text",
            )

        # Try each extraction method in order
        if self._use_code_blocks:
            result = self._extract_from_code_block(text)
            if result.data is not None:
                return result

        if self._use_output_tags:
            result = self._extract_from_tag(text, _OUTPUT_TAG_PATTERN, "output_tag")
            if result.data is not None:
                return result

        if self._use_result_tags:
            result = self._extract_from_tag(text, _RESULT_TAG_PATTERN, "result_tag")
            if result.data is not None:
                return result

        if self._use_inline:
            result = self._extract_inline(text)
            if result.data is not None:
                return result

        if self._use_raw:
            result = self._extract_raw(text)
            if result.data is not None:
                return result

        return ExtractionResult(
            data=None,
            format_found=None,
            raw_match=None,
            error="No valid JSON found in text",
        )

    def _extract_from_code_block(self, text: str) -> ExtractionResult:
        """Extract JSON from code blocks."""
        for match in _JSON_CODE_BLOCK_PATTERN.finditer(text):
            body = match.group("body") or ""
            body = body.strip()

            # Try parsing as-is
            try:
                parsed = json.loads(body)
                return ExtractionResult(
                    data=parsed,
                    format_found="code_block",
                    raw_match=body[:200],
                    error=None,
                )
            except json.JSONDecodeError:
                pass

            # Try stripping outer whitespace
            try:
                parsed = json.loads(body.strip())
                return ExtractionResult(
                    data=parsed,
                    format_found="code_block",
                    raw_match=body[:200],
                    error=None,
                )
            except json.JSONDecodeError:
                pass

        return ExtractionResult(
            data=None,
            format_found=None,
            raw_match=None,
            error="No valid JSON in code blocks",
        )

    def _extract_from_tag(
        self,
        text: str,
        pattern: re.Pattern[str],
        tag_name: str,
    ) -> ExtractionResult:
        """Extract JSON from XML-style tags."""
        match = pattern.search(text)
        if not match:
            return ExtractionResult(
                data=None,
                format_found=None,
                raw_match=None,
                error=f"No <{tag_name}> tag found",
            )

        body = match.group(1) or ""
        body = body.strip()

        try:
            parsed = json.loads(body)
            return ExtractionResult(
                data=parsed,
                format_found=tag_name,
                raw_match=body[:200],
                error=None,
            )
        except json.JSONDecodeError as e:
            return ExtractionResult(
                data=None,
                format_found=tag_name,
                raw_match=body[:200],
                error=f"Invalid JSON in {tag_name} tag: {e}",
            )

    def _extract_inline(self, text: str) -> ExtractionResult:
        """Extract JSON object from inline text."""
        # Find first { that could be JSON start
        first_brace = text.find("{")
        if first_brace == -1:
            return ExtractionResult(
                data=None,
                format_found=None,
                raw_match=None,
                error="No '{' found for inline JSON",
            )

        # Try to extract JSON starting from first brace
        # Use a greedy approach to find matching close brace
        candidate = text[first_brace:]

        # Try progressively larger slices
        for end_offset in self._find_json_ends(candidate):
            slice_text = candidate[:end_offset]
            try:
                parsed = json.loads(slice_text)
                return ExtractionResult(
                    data=parsed,
                    format_found="inline",
                    raw_match=slice_text[:200],
                    error=None,
                )
            except json.JSONDecodeError:
                continue

        return ExtractionResult(
            data=None,
            format_found=None,
            raw_match=None,
            error="Could not parse inline JSON",
        )

    def _find_json_ends(self, text: str) -> list[int]:
        """Find potential JSON end positions by counting brace depth."""
        ends: list[int] = []
        depth = 0
        in_string = False
        escape_next = False

        for i, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue

            if char == "\\" and in_string:
                escape_next = True
                continue

            if char == '"':
                in_string = not in_string
                continue

            if in_string:
                continue

            if char in '{["':
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    ends.append(i + 1)

        return ends

    def _extract_raw(self, text: str) -> ExtractionResult:
        """Try parsing entire text as JSON."""
        text = text.strip()

        try:
            parsed = json.loads(text)
            return ExtractionResult(
                data=parsed,
                format_found="raw",
                raw_match=text[:200],
                error=None,
            )
        except json.JSONDecodeError as e:
            return ExtractionResult(
                data=None,
                format_found=None,
                raw_match=text[:200],
                error=f"Raw JSON parse failed: {e}",
            )
