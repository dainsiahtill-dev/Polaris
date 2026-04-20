"""Edit Replacers module.

This module provides multiple strategies for finding text in content,
with a fallback chain that tries strategies from most precise to least.

Reference: OpenCode packages/opencode/src/tool/edit.ts
"""

from polaris.kernelone.editing.replacers.base import EditReplacer
from polaris.kernelone.editing.replacers.opencode_replacers import (
    DEFAULT_REPLACERS,
    BlockAnchorReplacer,
    ContextAwareReplacer,
    EscapeNormalizedReplacer,
    IndentationFlexibleReplacer,
    LineTrimmedReplacer,
    MultiOccurrenceReplacer,
    # Replacer implementations
    SimpleReplacer,
    TrimmedBoundaryReplacer,
    WhitespaceNormalizedReplacer,
    get_replacer_chain,
    # Utilities
    levenshtein_distance,
    normalize_line_endings,
    split_lines,
    string_similarity,
)

__all__ = [
    "DEFAULT_REPLACERS",
    "BlockAnchorReplacer",
    "ContextAwareReplacer",
    # Base
    "EditReplacer",
    "EscapeNormalizedReplacer",
    "IndentationFlexibleReplacer",
    "LineTrimmedReplacer",
    "MultiOccurrenceReplacer",
    # Replacers
    "SimpleReplacer",
    "TrimmedBoundaryReplacer",
    "WhitespaceNormalizedReplacer",
    "get_replacer_chain",
    # Utilities
    "levenshtein_distance",
    "normalize_line_endings",
    "split_lines",
    "string_similarity",
]
